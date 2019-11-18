'use strict';

// Register `issueList` component, along with its associated controller and template
angular.
  module('pullrequestsList').
  component('pullrequestsList', {
    templateUrl: 'ng/pullrequests-list/pullrequests-list.template.html',
    controller: ['$http', '$routeParams', '$rootScope', function PullrequestsListController($http, $routeParams, $rootScope) {
        var self = this;
        self.orderProp = 'id';
        self.reverseSort = false;
        self.tableCols = [
            {title:"Title", index:"id"},
            {title:"Author", index:"author.display_name"},
            // {title:"Source", index:"source.repository.full_name"},
            // {title:"Destination", index:"destination.branch.name"},
            {title:"Status", index:"state"},
            {title:"Created", index:"created_on"},
            {title:"Updated", index:"updated_on"},
            {title:"Comments", index:"comment_count"},
            {title:"Closed By", index:"closed_by.display_name"},
        ];

        //pagination info
        self.currentPage = $routeParams.pageId;
        self.project_slug = $routeParams.owner + '/' + $routeParams.project;
        
        $http.get($rootScope.projects[self.project_slug]['project_path']+'pullrequests_state=MERGED&state=OPEN&state=SUPERSEDED&state=DECLINED&page='+self.currentPage+'.json').then(function(response) {
            self.pullrequests = response.data;
        });
        
    }]
  });