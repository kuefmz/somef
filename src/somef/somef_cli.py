import sys
import validators
import logging
import os
import tempfile

from os import path
from . import header_analysis, regular_expressions, process_repository, configuration, process_files, \
    supervised_classification
from .process_results import Result
from .utils import constants, markdown_utils
from .parser import mardown_parser, create_excerpts
from .export.data_to_graph import DataGraph
from .export import json_export


def is_in_excerpts_headers(text, set_excerpts):
    """
    Function that checks if some text is included in a set of excerpts
    Parameters
    ----------
    text: text to look for
    set_excerpts: existing set of excerpts

    Returns
    -------
    True if the text is included in the excerpts, False otherwise.
    """
    set_text = set(text.split())
    for excerpt in set_excerpts:
        set_excerpt = set(excerpt.split())
        if set_text.issubset(set_excerpt):
            return True, excerpt

    return False, None


def merge(header_predictions, predictions, citations, citation_file_text, dois, binder_links, long_title,
          readthedocs_links, repo_status, logo, images, support_channels, package_distribution,
          wiki_links, category):
    """
    Function that takes the predictions using header information, classifier and bibtex/doi parser
    Parameters
    ----------
    header_predictions: extraction of common headers and their contents
    wiki_links: links to wikis
    package_distribution: packages that appear in the readme
    support_channels: like gitter, discord, etc.
    images: included in the readme
    logo: included in the readme
    repo_status: repostatus.org badges
    readthedocs_links: documentation links
    long_title: title of the repository
    binder_links: links to binder notebooks
    citation_file_text: text of the citation file
    header_predictions: predicted headers
    predictions: predictions from classifiers (description, installation instructions, invocation, citation)
    citations: bibtex citations
    dois: identifiers found in readme Zenodo DOIs, or other
    category: prediction of the category of the given repo
    Returns
    -------
    Combined predictions and results of the extraction process
    """
    print("Merge prediction using header information, classifier and bibtex and doi parsers")
    if long_title:
        predictions['longTitle'] = {'excerpt': long_title, 'confidence': [1.0],
                                    'technique': 'Regular expression'}
    for i in range(len(citations)):
        if 'citation' not in predictions.keys():
            predictions['citation'] = []
        if citations[i].find('https://doi.org/') >= 0 or citations[i].find('doi ') >= 0:
            doi_text = ""
            text_citation = citations[i]
            if text_citation.find("https://doi.org/") >= 0:
                doi_pos = text_citation.find("doi.org/")
                starts = text_citation[:doi_pos].rindex("http")
                ends = text_citation[starts:].find("}")
                doi_text = text_citation[starts:starts + ends]
            elif text_citation.find("doi") >= 0:
                doi_pos = text_citation.find("doi")
                starts = text_citation[doi_pos:].find("{")
                ends = text_citation[starts + doi_pos:].find("}")
                doi_text = "https://doi.org/" + text_citation[starts + doi_pos + 1:doi_pos + starts + ends]
            predictions['citation'].append({'excerpt': citations[i], 'confidence': [1.0],
                                            'technique': 'Regular expression', 'doi': doi_text,
                                            'format': 'bibtex'})
        else:
            predictions['citation'].append({'excerpt': citations[i], 'confidence': [1.0],
                                            'technique': 'Regular expression', 'format': 'bibtex'})
    if len(citation_file_text) != 0:
        if 'citation' not in predictions.keys():
            predictions['citation'] = []
        predictions['citation'].append({'excerpt': citation_file_text, 'confidence': [1.0],
                                        'technique': 'File Exploration', 'format': 'citation file format'})
    if len(dois) != 0:
        predictions['identifier'] = []
        for identifier in dois:
            # The identifier is in position 1. Position 0 is the badge id, which we don't want to export
            predictions['identifier'].append({'excerpt': identifier[1], 'confidence': [1.0],
                                              'technique': 'Regular expression'})
    if len(binder_links) != 0:
        predictions['executableExample'] = {'excerpt': binder_links, 'confidence': [1.0],
                                            'technique': 'Regular expression'}
    if len(repo_status) != 0:
        predictions['repoStatus'] = {
            'excerpt': "https://www.repostatus.org/#" + repo_status[0:repo_status.find(" ")].lower(),
            'description': repo_status,
            'confidence': [1.0],
            'technique': 'Regular expression'}

    # Commenting this out because arxiv links without context are not useful.
    # if len(arxiv_links) != 0:
    #     predictions['arxivLinks'] = {'excerpt': arxiv_links, 'confidence': [1.0],
    #                                  'technique': 'Regular expression'}

    if len(logo) != 0:
        predictions['logo'] = {'excerpt': logo, 'confidence': [1.0],
                               'technique': 'Regular expression'}

    if len(images) != 0:
        badges = []
        for image in images:
            if image.find('badge') >= 0:
                badges.append(image)
        for badge in badges:
            images.remove(badge)
        if len(images) > 0:
            predictions['image'] = []
            for image in images:
                predictions['image'].append({'excerpt': image, 'confidence': [1.0],
                                             'technique': 'Regular expression'})

    if len(support_channels) != 0:
        predictions['supportChannels'] = {'excerpt': support_channels, 'confidence': [1.0],
                                          'technique': 'Regular expression'}

    if len(package_distribution) != 0:
        predictions['packageDistribution'] = {'excerpt': package_distribution, 'confidence': [1.0],
                                              'technique': 'Regular expression'}

    for i in range(len(readthedocs_links)):
        if 'documentation' not in predictions.keys():
            predictions['documentation'] = []
        predictions['documentation'].append({'excerpt': readthedocs_links[i], 'confidence': [1.0],
                                             'technique': 'Regular expression', 'type': 'readthedocs'})

    for i in range(len(wiki_links)):
        if 'documentation' not in predictions.keys():
            predictions['documentation'] = []
        predictions['documentation'].append({'excerpt': wiki_links[i], 'confidence': [1.0],
                                             'technique': 'Regular expression', 'type': 'wiki'})

    if category:
        predictions['category'] = category

    for headers in header_predictions:
        if headers not in predictions.keys():
            predictions[headers] = header_predictions[headers]
        else:
            for h in header_predictions[headers]:
                predictions[headers].insert(0, h)
    print("Merging successful. \n")
    return predictions


def format_output(git_data, repo_data, repo_type):
    """
    Function takes metadata, readme text predictions, bibtex citations and path to the output file
    Parameters
    ----------
    git_data GitHub obtained data
    repo_data Data extracted from the code repo by SOMEF

    Returns
    -------
    json representation of the categories found in file
    """
    text_technique = 'GitHub API'
    if repo_type is constants.RepositoryType.GITLAB:
        text_technique = 'GitLab API'
    print("formatting output")

    for i in git_data.keys():
        if i == 'description':
            if 'description' not in repo_data.keys():
                repo_data['description'] = []
            if git_data[i] != "":
                repo_data['description'].append(
                    {'excerpt': git_data[i], 'confidence': [1.0], 'technique': text_technique})
        else:
            keys = repo_data.keys
            if i in constants.file_exploration:
                if i == 'hasExecutableNotebook':
                    repo_data[i] = {'excerpt': git_data[i], 'confidence': [1.0], 'technique': 'File Exploration',
                                    'format': 'jupyter notebook'}
                elif i == 'hasBuildFile':
                    docker_files = []
                    docker_compose = []
                    for data in git_data[i]:
                        if data.lower().endswith('docker-compose.yml'):
                            docker_compose.append(data)
                        else:
                            docker_files.append(data)
                    repo_data[i] = []
                    if len(docker_files) > 0:
                        repo_data[i].append({'excerpt': docker_files, 'confidence': [1.0],
                                             'technique': 'File Exploration',
                                             'format': 'Docker file'})
                    if len(docker_compose) > 0:
                        repo_data[i].append({'excerpt': docker_compose, 'confidence': [1.0],
                                             'technique': 'File Exploration',
                                             'format': 'Docker compose file'})
                else:
                    if i in repo_data:
                        repo_data[i].append(
                            {'excerpt': git_data[i], 'confidence': [1.0], 'technique': 'File Exploration'})
                    else:
                        repo_data[i] = {'excerpt': git_data[i], 'confidence': [1.0], 'technique': 'File Exploration'}
            elif git_data[i] != "" and git_data[i] != []:
                repo_data[i] = {'excerpt': git_data[i], 'confidence': [1.0], 'technique': text_technique}
    # remove empty categories from json
    return remove_empty_elements(repo_data)


def remove_empty_elements(d):
    """recursively remove empty lists, empty dicts, or None elements from a dictionary"""

    def empty(x):
        return x is None or x == {} or x == []

    if not isinstance(d, (dict, list)):
        return d
    elif isinstance(d, list):
        return [v for v in (remove_empty_elements(v) for v in d) if not empty(v)]
    else:
        return {k: v for k, v in ((k, remove_empty_elements(v)) for k, v in d.items()) if not empty(v)}


def save_json(git_data, repo_data, outfile):
    """Performs some combinations and saves the final json Object in output file"""
    repo_data = format_output(git_data, repo_data)
    json_export.save_json_output(repo_data, outfile, None)


def cli_get_data(threshold, ignore_classifiers, repo_url=None, doc_src=None, local_repo=None,
                 ignore_github_metadata=False, readme_only=False, keep_tmp=None) -> Result:
    """
    Main function to get the data through the command line
    Parameters
    ----------
    @param threshold: threshold to filter annotations. 0.8 by default
    @param ignore_classifiers: flag to indicate if the output from the classifiers should be ignored
    @param repo_url: URL of the repository to analyze
    @param doc_src: path to the src of the target repo
    @param local_repo: flag to indicate that the repo is local
    @param ignore_github_metadata: flag used to avoid doing extra requests to the GitHub API
    @param readme_only: flag to indicate that only the readme should be analyzed
    @param keep_tmp: path where to store TMP files in case SOMEF is instructed to keep them

    Returns
    -------
    @return: Dictionary with the results found by SOMEF, formatted as a Result object.
    """
    file_paths = configuration.get_configuration_file()
    repo_type = constants.RepositoryType.GITHUB
    repository_metadata = Result()
    if repo_url is not None:
        try:
            if repo_url.rfind("gitlab.com") > 0:
                repo_type = constants.RepositoryType.GITLAB
            repository_metadata, owner, repo_name, def_branch = process_repository.load_online_repository_metadata(
                repository_metadata,
                repo_url,
                ignore_github_metadata,
                repo_type)
            # download files and obtain path to download folder
            if readme_only:
                # download readme only with the information above
                readme_text = process_repository.download_readme(owner, repo_name, def_branch, repo_type)

            elif keep_tmp is not None:  # save downloaded files locally
                os.makedirs(keep_tmp, exist_ok=True)
                local_folder = process_repository.download_repository_files(owner, repo_name, def_branch, repo_type,
                                                                            keep_tmp, repo_url)
                readme_text, full_repository_metadata = process_files.process_repository_files(local_folder,
                                                                                               repository_metadata,
                                                                                               repo_type, owner,
                                                                                               repo_name,
                                                                                               def_branch)
            else:  # Use a temp directory
                with tempfile.TemporaryDirectory() as temp_dir:
                    local_folder = process_repository.download_repository_files(owner, repo_name, def_branch, repo_type,
                                                                                temp_dir, repo_url)
                    readme_text, full_repository_metadata = process_files.process_repository_files(local_folder,
                                                                                                   repository_metadata,
                                                                                                   repo_type, owner,
                                                                                                   repo_name,
                                                                                                   def_branch)
            if readme_text == "":
                logging.warning("README document does not exist in the target repository")
        except process_repository.GithubUrlError:
            logging.error("Error processing the target repository")
            return repository_metadata
    elif local_repo is not None:
        try:
            readme_text, full_repository_metadata = process_files.process_repository_files(local_repo,
                                                                                           repository_metadata,
                                                                                           repo_type)
            if readme_text == "":
                logging.warning("Warning: README document does not exist in the local repository")
        except process_repository.GithubUrlError:
            logging.error("Error processing the input repository")
            return repository_metadata
    else:
        if doc_src is None or not path.exists(doc_src):
            logging.error("Error processing the input repository")
            sys.exit()
        with open(doc_src, 'r', encoding="UTF-8") as doc_fh:
            readme_text = doc_fh.read()
        repository_metadata = {}
    try:
        unfiltered_text = readme_text
        repository_metadata, string_list = header_analysis.extract_categories(unfiltered_text, repository_metadata)
        readme_text = markdown_utils.unmark(readme_text)
        repository_metadata = supervised_classification.run_category_classification(unfiltered_text, threshold,
                                                                                    repository_metadata)
        print(string_list)
        excerpts = create_excerpts.create_excerpts(string_list)
        if not ignore_classifiers or unfiltered_text != '':
            excerpts_headers = mardown_parser.extract_text_excerpts_header(unfiltered_text)
            header_parents = mardown_parser.extract_headers_parents(unfiltered_text)
            score_dict = supervised_classification.run_classifiers(excerpts, file_paths)
            repository_metadata = supervised_classification.classify(score_dict, threshold, excerpts_headers,
                                                                     header_parents, repository_metadata)
        if readme_text != "":
            try:
                readme_source = repository_metadata.results[constants.CAT_README_URL][0]
                readme_source = readme_source[constants.PROP_RESULT][constants.PROP_VALUE]
            except:
                readme_source = "README.md"
            repository_metadata = regular_expressions.extract_bibtex(readme_text, repository_metadata, readme_source)
            repository_metadata = regular_expressions.extract_doi_badges(unfiltered_text, repository_metadata,
                                                                         readme_source)
            repository_metadata = regular_expressions.extract_title(unfiltered_text, repository_metadata, readme_source)
            repository_metadata = regular_expressions.extract_binder_links(unfiltered_text, repository_metadata,
                                                                           readme_source)
            repository_metadata = regular_expressions.extract_readthedocs(unfiltered_text, repository_metadata,
                                                                          readme_source)
            logging.info("Completed extracting regular expressions")
            return repository_metadata
        #
        #     readthedocs_links = regular_expressions.extract_readthedocs(unfiltered_text)
        #     repo_status = regular_expressions.extract_repo_status(unfiltered_text)
        #     wiki_links = regular_expressions.extract_wiki_links(unfiltered_text, repo_url)
        #     logo, images = regular_expressions.extract_images(unfiltered_text, repo_url, local_repo)
        #     support_channels = regular_expressions.extract_support_channels(unfiltered_text)
        #     package_distribution = regular_expressions.extract_package_distributions(unfiltered_text)

        # else:
        #     citations = []
        #     citation_file_text = ""
        #     dois = []
        #     binder_links = []
        #     title = ""
        #     readthedocs_links = []
        #     repo_status = ""
        #    # arxiv_links = []
        #     wiki_links = []
        #     logo = ""
        #     images = []
        #     support_channels = []
        #     package_distribution = ""
        # predictions = merge(header_predictions, predictions, citations, citation_file_text, dois, binder_links, title,
        #                     readthedocs_links, repo_status, logo, images, support_channels,
        #                     package_distribution, wiki_links, category)
        # return format_output(repository_metadata, predictions, repo_type)
    except Exception as e:
        logging.error("Error processing repository " + str(e))
        return repository_metadata


def run_cli_document(doc_src, threshold, output):
    """Runs all the required components of the cli on a given document file"""
    return run_cli(threshold=threshold, output=output, doc_src=doc_src)


def run_cli(*,
            threshold=0.8,
            ignore_classifiers=False,
            repo_url=None,
            ignore_github_metadata=False,
            readme_only=False,
            doc_src=None,
            local_repo=None,
            in_file=None,
            output=None,
            graph_out=None,
            graph_format="turtle",
            codemeta_out=None,
            pretty=False,
            missing=False,
            keep_tmp=None
            ):
    """Function to run all the required components of the cli for a repository"""
    # check if it is a valid url
    if repo_url:
        if not validators.url(repo_url):
            logging.error("Not a valid repository url. Please check the url provided")
            return None
    multiple_repos = in_file is not None
    if multiple_repos:
        with open(in_file, "r") as in_handle:
            # get the line (with the final newline omitted) if the line is not empty
            repo_list = [line[:-1] for line in in_handle if len(line) > 1]

        # convert to a set to ensure uniqueness (we don't want to get the same data multiple times)
        repo_set = set(repo_list)
        # check if the urls in repo_set if are valid
        remove_urls = []
        for repo_elem in repo_set:
            if not validators.url(repo_elem):
                logging.error("Not a valid repository url. Please check the url provided: " + repo_elem)
                remove_urls.append(repo_elem)
        # remove non valid urls in repo_set
        for remove_url in remove_urls:
            repo_set.remove(remove_url)
        if len(repo_set) > 0:
            repo_data = [cli_get_data(threshold=threshold, ignore_classifiers=ignore_classifiers, repo_url=repo_url,
                                      keep_tmp=keep_tmp) for repo_url
                         in repo_set]
        else:
            return None

    else:
        if repo_url:
            repo_data = cli_get_data(threshold=threshold, ignore_classifiers=ignore_classifiers, repo_url=repo_url,
                                     ignore_github_metadata=ignore_github_metadata, readme_only=readme_only,
                                     keep_tmp=keep_tmp)
        elif local_repo:
            repo_data = cli_get_data(threshold=threshold, ignore_classifiers=ignore_classifiers,
                                     local_repo=local_repo, keep_tmp=keep_tmp)
        else:
            repo_data = cli_get_data(threshold=threshold, ignore_classifiers=ignore_classifiers,
                                     doc_src=doc_src, keep_tmp=keep_tmp)

    if output is not None:
        json_export.save_json_output(repo_data, output, missing, pretty=pretty)

    if graph_out is not None:
        logging.info("Generating Knowledge Graph")
        data_graph = DataGraph()
        if multiple_repos:
            for repo in repo_data:
                data_graph.add_somef_data(repo)
        else:
            data_graph.add_somef_data(repo_data)

        logging.info("Saving Knowledge Graph ttl data to", graph_out)
        with open(graph_out, "wb") as out_file:
            out_file.write(data_graph.g.serialize(format=graph_format, encoding="UTF-8"))

    if codemeta_out is not None:
        json_export.save_codemeta_output(repo_data, codemeta_out, pretty=pretty)
